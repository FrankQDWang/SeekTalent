"""Main-only lifecycle authority for one ready local sidecar child."""

from __future__ import annotations

import subprocess
import secrets
import threading
import time
import weakref
from dataclasses import dataclass
from typing import IO, Never, SupportsIndex

from seektalent.installed_slot import InstalledSidecarLaunchLease
from seektalent.owned_sidecar_process import OwnedSidecarProcess, SidecarSpawnCleanupError, spawn_owned_sidecar
from seektalent.sidecar_child_session import SidecarHandshakeResult, serve_sidecar_handshake
from seektalent.sidecar_handshake_protocol import (
    DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
    MAX_HANDSHAKE_FRAME_BYTES,
    SidecarHandshakeIdentity,
    SidecarReadinessError,
    SidecarReadinessReason,
    _ProtocolTransport,
    _retain_unclosed_transport,
    _new_main_hello as _new_protocol_main_hello,
    _validated_timeout,
    perform_main_handshake,
)
from seektalent.source_port.authenticated_history_frames import (
    PostHandshakeHistorySession,
    ReceivedHistoryMessage,
    ReceivedHistoryResult,
    SourceHistoryAdmissionError,
    SourceHistoryAdmissionReason,
)
from seektalent.source_port.history_contract import (
    SourceHistoryIdentityConflict,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryV1,
    SourceHistoryUnavailable,
)


_STDERR_CAPTURE_BYTES = 64 * 1024

__all__ = [
    "DEFAULT_HANDSHAKE_TIMEOUT_SECONDS",
    "AdmittedSourceHistoryResult",
    "MAX_HANDSHAKE_FRAME_BYTES",
    "ReadySidecarSession",
    "SidecarHandshakeIdentity",
    "SidecarHandshakeResult",
    "SidecarReadinessError",
    "SidecarReadinessReason",
    "SourceHistoryAdmissionError",
    "SourceHistoryAdmissionReason",
    "exchange_source_history",
    "serve_sidecar_handshake",
    "spawn_ready_sidecar",
]


@dataclass(slots=True)
class _ReadySidecarState:
    process: OwnedSidecarProcess
    transport: _ProtocolTransport
    session_id: str
    protocol_minor: int
    history: PostHandshakeHistorySession
    stderr_drain: "_BoundedStderrDrain"
    history_exchange_lock: threading.Lock
    history_query_in_flight: bool = False
    history_exchange_usable: bool = True
    cleanup_error: SidecarSpawnCleanupError | None = None


_READY_SESSIONS: dict[int, tuple[weakref.ReferenceType["ReadySidecarSession"], _ReadySidecarState]] = {}
_READY_SESSIONS_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class _AdmittedSourceHistoryState:
    ready_session: weakref.ReferenceType["ReadySidecarSession"]
    session_id: str
    query_message_id: str
    result_message_id: str
    reply_to: str
    correlation_id: str | None
    query: SourceHistoryQueryV1
    payload: SourceHistoryMatched | SourceHistoryNotFound | SourceHistoryIdentityConflict | SourceHistoryUnavailable


_ADMITTED_RESULTS: dict[
    int,
    tuple[weakref.ReferenceType["AdmittedSourceHistoryResult"], _AdmittedSourceHistoryState],
] = {}
_ADMITTED_RESULTS_LOCK = threading.Lock()


class AdmittedSourceHistoryResult:
    """Factory-only result authenticated by one exact ready sidecar session."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("AdmittedSourceHistoryResult is factory-only")

    @property
    def session_id(self) -> str:
        return _admitted_state(self).session_id

    @property
    def query_message_id(self) -> str:
        return _admitted_state(self).query_message_id

    @property
    def result_message_id(self) -> str:
        return _admitted_state(self).result_message_id

    @property
    def reply_to(self) -> str:
        return _admitted_state(self).reply_to

    @property
    def correlation_id(self) -> str | None:
        return _admitted_state(self).correlation_id

    @property
    def query(self) -> SourceHistoryQueryV1:
        return _admitted_state(self).query

    @property
    def payload(
        self,
    ) -> SourceHistoryMatched | SourceHistoryNotFound | SourceHistoryIdentityConflict | SourceHistoryUnavailable:
        return _admitted_state(self).payload

    def __copy__(self) -> Never:
        raise TypeError("AdmittedSourceHistoryResult cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("AdmittedSourceHistoryResult cannot be copied")

    def __reduce_ex__(self, _: SupportsIndex) -> Never:
        raise TypeError("AdmittedSourceHistoryResult cannot be serialized")


class ReadySidecarSession:
    """Factory-only main authority over one ready child and its one protocol transport."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("ReadySidecarSession is factory-only")

    @property
    def pid(self) -> int:
        return _ready_state(self).process.pid

    @property
    def session_id(self) -> str:
        return _ready_state(self).session_id

    @property
    def protocol_minor(self) -> int:
        return _ready_state(self).protocol_minor

    def new_history_session(self) -> PostHandshakeHistorySession:
        """Return the sole persistent history state for this transport."""
        return _ready_state(self).history

    def send_history_frame(self, frame: bytes, *, timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS) -> None:
        state = _ready_state(self)
        deadline = time.monotonic() + _validated_timeout(timeout)
        state.transport.write_raw(frame, deadline)

    def receive_history(self, *, timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS) -> tuple[ReceivedHistoryMessage, ...]:
        state = _ready_state(self)
        deadline = time.monotonic() + _validated_timeout(timeout)
        try:
            chunk = state.transport.read_history_chunk(deadline, state.process)
        except SidecarReadinessError as error:
            if error.reason is SidecarReadinessReason.EOF:
                state.history.feed_eof()
            raise
        return state.history.feed(chunk)

    def close(self, timeout: float) -> int:
        """Kill/reap/release exactly once, retaining cleanup authority on any failure."""
        state = _ready_state(self)
        if state.cleanup_error is not None:
            if not state.cleanup_error.reap() or not state.cleanup_error.lease_released:
                raise SidecarReadinessError(
                    SidecarReadinessReason.CHILD_FAILURE,
                    cleanup_error=state.cleanup_error,
                )
            return _finish_ready_close(self, state)
        try:
            return_code = state.process.kill(timeout)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as primary_error:
            if state.process.returncode is not None and state.process._lease_state is not None:
                cleanup_error = state.process._retain_reaped_cleanup(primary_error)
            else:
                cleanup_error = state.process._cleanup_after_handshake_failure(primary_error)
            if cleanup_error is not None:
                state.cleanup_error = cleanup_error
                raise SidecarReadinessError(
                    SidecarReadinessReason.CHILD_FAILURE,
                    cleanup_error=cleanup_error,
                ) from None
            return_code = state.process.returncode
            if return_code is None:
                raise AssertionError("successful ready-session cleanup has no child return code")
        if not state.transport.close():
            raise SidecarReadinessError(SidecarReadinessReason.PIPE_IO_FAILURE)
        _discard_ready_state(self)
        return return_code

    def __copy__(self) -> Never:
        raise TypeError("ReadySidecarSession cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("ReadySidecarSession cannot be copied")

    def __reduce_ex__(self, _: SupportsIndex) -> Never:
        raise TypeError("ReadySidecarSession cannot be serialized")


def exchange_source_history(
    session: ReadySidecarSession,
    query: SourceHistoryQueryV1,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
) -> AdmittedSourceHistoryResult:
    """Run one authenticated query/reply exchange on an exact live ready session."""
    normalized_timeout = _validated_timeout(timeout)
    state = _ready_state(session)
    if not isinstance(query, SourceHistoryQueryV1):
        raise TypeError("query must be a SourceHistoryQueryV1")
    _begin_history_exchange(state)
    succeeded = False
    try:
        deadline = time.monotonic() + normalized_timeout
        query_message_id = secrets.token_hex(16)
        correlation_id = secrets.token_hex(16)
        frame = state.history.encode_query(
            message_id=query_message_id,
            correlation_id=correlation_id,
            payload=query,
        )
        session.send_history_frame(frame, timeout=_remaining(deadline))
        received = _receive_one_history_result(session, deadline)
        admitted = _new_admitted_result(
            session,
            query=query,
            query_message_id=query_message_id,
            received=received,
        )
        succeeded = True
        return admitted
    finally:
        _finish_history_exchange(state, succeeded=succeeded)


def _receive_one_history_result(session: ReadySidecarSession, deadline: float) -> ReceivedHistoryResult:
    while True:
        messages = session.receive_history(timeout=_remaining(deadline))
        if not messages:
            continue
        if len(messages) != 1:
            raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.MULTIPLE_MESSAGES)
        message = messages[0]
        if not isinstance(message, ReceivedHistoryResult):
            raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.UNEXPECTED_MESSAGE)
        return message


def _begin_history_exchange(state: _ReadySidecarState) -> None:
    with state.history_exchange_lock:
        if state.history_query_in_flight:
            raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.QUERY_IN_FLIGHT)
        if not state.history_exchange_usable:
            raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.SESSION_UNUSABLE)
        state.history_query_in_flight = True


def _finish_history_exchange(state: _ReadySidecarState, *, succeeded: bool) -> None:
    with state.history_exchange_lock:
        state.history_query_in_flight = False
        if not succeeded:
            state.history_exchange_usable = False


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SidecarReadinessError(SidecarReadinessReason.READ_TIMEOUT)
    return remaining


def spawn_ready_sidecar(
    lease: InstalledSidecarLaunchLease,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
) -> ReadySidecarSession:
    """Spawn one admitted child and hand its sole protocol transport to a ready session."""
    normalized_timeout = _validated_timeout(timeout)
    if not isinstance(lease, InstalledSidecarLaunchLease):
        raise TypeError("lease must be an InstalledSidecarLaunchLease")
    try:
        process = spawn_owned_sidecar(lease)
    except SidecarSpawnCleanupError as exc:
        raise SidecarReadinessError(SidecarReadinessReason.CHILD_FAILURE, cleanup_error=exc) from None
    except (OSError, RuntimeError, ValueError):
        raise SidecarReadinessError(SidecarReadinessReason.CHILD_FAILURE) from None

    transport: _ProtocolTransport | None = None
    try:
        deadline = time.monotonic() + normalized_timeout
        stderr_drain = _BoundedStderrDrain(process.stderr_reader)
        stderr_drain.start()
        transport = _ProtocolTransport(process.protocol_reader, process.protocol_writer)
        expected_identity = _identity_from_admission(lease.admission)
        material = perform_main_handshake(
            transport,
            expected_identity,
            product_build_id=lease.admission.product_build_id,
            main_application_build_id=lease.admission.main_application_build_id,
            deadline=deadline,
            process=process,
        )
        history = PostHandshakeHistorySession.for_main(
            session_id=material.session_id,
            protocol_minor=material.protocol_minor,
            main_to_sidecar_key=material.main_to_sidecar_key,
            sidecar_to_main_key=material.sidecar_to_main_key,
        )
        return _new_ready_session(
            _ReadySidecarState(
                process=process,
                transport=transport,
                session_id=material.session_id,
                protocol_minor=material.protocol_minor,
                history=history,
                stderr_drain=stderr_drain,
                history_exchange_lock=threading.Lock(),
            )
        )
    except SidecarReadinessError as error:
        _cleanup_failed_readiness(process, transport, error)
        raise
    except (OSError, RuntimeError, ValueError):
        error = SidecarReadinessError(SidecarReadinessReason.PIPE_IO_FAILURE)
        _cleanup_failed_readiness(process, transport, error)
        raise error from None


def _identity_from_admission(admission: object) -> SidecarHandshakeIdentity:
    protocol = getattr(admission, "source_port_protocol")
    return SidecarHandshakeIdentity(
        product_build_id=getattr(admission, "product_build_id"),
        sidecar_build_id=getattr(admission, "sidecar_build_id"),
        protocol_id=getattr(protocol, "protocol_id"),
        protocol_major=getattr(protocol, "major"),
        protocol_min_minor=getattr(protocol, "min_minor"),
        protocol_max_minor=getattr(protocol, "max_minor"),
        protocol_capabilities=getattr(protocol, "capabilities"),
        expected_main_application_build_id=getattr(admission, "main_application_build_id"),
    )


def _new_main_hello(admission: object) -> tuple[dict[str, object], bytes]:
    """Test seam for the protocol-owned fresh MainHello material."""
    return _new_protocol_main_hello(
        getattr(admission, "product_build_id"),
        getattr(admission, "main_application_build_id"),
    )


def _cleanup_failed_readiness(
    process: OwnedSidecarProcess,
    transport: _ProtocolTransport | None,
    error: SidecarReadinessError,
) -> None:
    if transport is not None:
        if not transport.close():
            _retain_unclosed_transport(transport)
    cleanup_error = process._cleanup_after_handshake_failure(error)
    if cleanup_error is not None:
        error.cleanup_error = cleanup_error


class _BoundedStderrDrain:
    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream
        self._captured = bytearray()

    def start(self) -> None:
        def drain() -> None:
            read = getattr(self._stream, "read1", self._stream.read)
            try:
                while chunk := read(4096):
                    remaining = _STDERR_CAPTURE_BYTES - len(self._captured)
                    if remaining > 0:
                        self._captured.extend(chunk[:remaining])
            except (OSError, ValueError):
                return

        threading.Thread(target=drain, daemon=True).start()


def _new_ready_session(state: _ReadySidecarState) -> ReadySidecarSession:
    session = object.__new__(ReadySidecarSession)
    session_id = id(session)

    def finalize(_: weakref.ReferenceType[ReadySidecarSession]) -> None:
        with _READY_SESSIONS_LOCK:
            entry = _READY_SESSIONS.pop(session_id, None)
        if entry is not None:
            _finalize_ready_state(entry[1])

    with _READY_SESSIONS_LOCK:
        _READY_SESSIONS[session_id] = (weakref.ref(session, finalize), state)
    return session


def _new_admitted_result(
    session: ReadySidecarSession,
    *,
    query: SourceHistoryQueryV1,
    query_message_id: str,
    received: ReceivedHistoryResult,
) -> AdmittedSourceHistoryResult:
    ready_state = _ready_state(session)
    if received.reply_to != query_message_id:
        raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.UNEXPECTED_MESSAGE)
    admitted = object.__new__(AdmittedSourceHistoryResult)
    admitted_id = id(admitted)
    state = _AdmittedSourceHistoryState(
        ready_session=weakref.ref(session),
        session_id=ready_state.session_id,
        query_message_id=query_message_id,
        result_message_id=received.message_id,
        reply_to=received.reply_to,
        correlation_id=received.correlation_id,
        query=query,
        payload=received.payload,
    )

    def finalize(_: weakref.ReferenceType[AdmittedSourceHistoryResult]) -> None:
        with _ADMITTED_RESULTS_LOCK:
            _ADMITTED_RESULTS.pop(admitted_id, None)

    with _ADMITTED_RESULTS_LOCK:
        _ADMITTED_RESULTS[admitted_id] = (weakref.ref(admitted, finalize), state)
    return admitted


def _admitted_state(result: AdmittedSourceHistoryResult) -> _AdmittedSourceHistoryState:
    with _ADMITTED_RESULTS_LOCK:
        entry = _ADMITTED_RESULTS.get(id(result))
    if entry is None or entry[0]() is not result:
        raise TypeError("AdmittedSourceHistoryResult must be a live factory result")
    return entry[1]


def _finalize_ready_state(state: _ReadySidecarState) -> None:
    try:
        if state.cleanup_error is not None:
            if not state.cleanup_error.reap():
                state.cleanup_error.abandon()
            return
        cleanup_error = state.process._cleanup_after_handshake_failure(
            SidecarReadinessError(SidecarReadinessReason.CHILD_FAILURE)
        )
        if cleanup_error is not None:
            cleanup_error.abandon()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return
    finally:
        if not state.transport.close():
            _retain_unclosed_transport(state.transport)


def _ready_state(session: ReadySidecarSession) -> _ReadySidecarState:
    with _READY_SESSIONS_LOCK:
        entry = _READY_SESSIONS.get(id(session))
    if entry is None or entry[0]() is not session:
        raise TypeError("ReadySidecarSession must be a live factory session")
    return entry[1]


def _finish_ready_close(session: ReadySidecarSession, state: _ReadySidecarState) -> int:
    return_code = state.process.returncode
    if return_code is None:
        raise AssertionError("reaped ready-session cleanup has no child return code")
    if not state.transport.close():
        raise SidecarReadinessError(SidecarReadinessReason.PIPE_IO_FAILURE)
    _discard_ready_state(session)
    return return_code


def _discard_ready_state(session: ReadySidecarSession) -> None:
    with _READY_SESSIONS_LOCK:
        _READY_SESSIONS.pop(id(session), None)
