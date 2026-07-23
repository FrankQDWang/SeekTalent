"""Main-only lifecycle authority for one ready local sidecar child."""

from __future__ import annotations

import subprocess
import threading
import time
import weakref
from dataclasses import dataclass
from typing import IO, Literal, Never, SupportsIndex

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
from seektalent.source_port.authenticated_source_port_session import (
    PostHandshakeSourcePortSession,
    ReceivedSourcePortMessage,
)
from seektalent.source_port.sidecar_transport import (
    SourcePortEndpoint,
    _register_source_port_endpoint,
    receive_source_port_messages,
    send_source_port_frame,
)


_STDERR_CAPTURE_BYTES = 64 * 1024

__all__ = [
    "DEFAULT_HANDSHAKE_TIMEOUT_SECONDS",
    "MAX_HANDSHAKE_FRAME_BYTES",
    "ReadySidecarSession",
    "SidecarHandshakeIdentity",
    "SidecarHandshakeResult",
    "SidecarReadinessError",
    "SidecarReadinessReason",
    "serve_sidecar_handshake",
    "spawn_ready_sidecar",
]


@dataclass(slots=True)
class _ReadySidecarState:
    process: OwnedSidecarProcess
    transport: _ProtocolTransport
    session_id: str
    protocol_minor: int
    source_port: PostHandshakeSourcePortSession
    stderr_drain: "_BoundedStderrDrain"
    source_port_exchange_lock: threading.Lock
    source_port_exchange_in_flight: bool = False
    source_port_exchange_usable: bool = True
    cleanup_error: SidecarSpawnCleanupError | None = None


_READY_SESSIONS: dict[int, tuple[weakref.ReferenceType["ReadySidecarSession"], _ReadySidecarState]] = {}
_READY_SESSIONS_LOCK = threading.Lock()

class ReadySidecarSession(SourcePortEndpoint):
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

    def source_port_session(self) -> PostHandshakeSourcePortSession:
        """Return the one shared post-handshake session for this child pipe."""
        return _ready_state(self).source_port

    def _send_source_port_frame(self, frame: bytes, deadline: float) -> None:
        _ready_state(self).transport.write_raw(frame, deadline)

    def _receive_source_port_messages(self, deadline: float) -> tuple[ReceivedSourcePortMessage, ...]:
        state = _ready_state(self)
        try:
            chunk = state.transport.read_history_chunk(deadline, state.process)
        except SidecarReadinessError as error:
            if error.reason is SidecarReadinessReason.EOF:
                state.source_port.feed_eof()
            raise
        return state.source_port.feed(chunk)

    def _begin_source_port_exchange(self) -> Literal["acquired", "in_flight", "unusable"]:
        state = _ready_state(self)
        with state.source_port_exchange_lock:
            if state.source_port_exchange_in_flight:
                return "in_flight"
            if not state.source_port_exchange_usable:
                return "unusable"
            state.source_port_exchange_in_flight = True
            return "acquired"

    def _finish_source_port_exchange(self, *, succeeded: bool) -> None:
        state = _ready_state(self)
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
        source_port = PostHandshakeSourcePortSession.for_main(
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
                source_port=source_port,
                stderr_drain=stderr_drain,
                source_port_exchange_lock=threading.Lock(),
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
    _register_source_port_endpoint(session)
    return session


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
