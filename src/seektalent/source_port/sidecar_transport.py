"""One post-handshake Source Port transport shared by history and verify-session."""

from __future__ import annotations

import inspect
import secrets
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Literal, Never, SupportsIndex, TypeAlias

from seektalent.sidecar_handshake_protocol import (
    DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
    SidecarReadinessError,
    SidecarReadinessReason,
    _validated_timeout,
)
from seektalent.source_port import authenticated_history_frames as history_frames
from seektalent.source_port import authenticated_verify_session_frames as verify_frames
from seektalent.source_port.authenticated_source_port_session import (
    PostHandshakeSourcePortSession,
    ReceivedSourcePortMessage,
    SourcePortTransportFrameError,
)
from seektalent.source_port.history_contract import (
    SourceHistoryIdentityConflict,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryV1,
    SourceHistoryUnavailable,
)
from seektalent.source_port.verify_session_contract import VerifySessionRequestV1


_HistoryResult = (
    SourceHistoryMatched | SourceHistoryNotFound | SourceHistoryIdentityConflict | SourceHistoryUnavailable
)
ReceivedVerifySessionTerminal: TypeAlias = (
    verify_frames.ReceivedVerifySessionRejected
    | verify_frames.ReceivedVerifySessionResult
    | verify_frames.ReceivedVerifySessionFailure
    | verify_frames.ReceivedVerifySessionReconcileRequired
)
ReceivedVerifySessionAcceptedTerminal: TypeAlias = (
    verify_frames.ReceivedVerifySessionResult
    | verify_frames.ReceivedVerifySessionFailure
    | verify_frames.ReceivedVerifySessionReconcileRequired
)


@dataclass(frozen=True, slots=True)
class VerifySessionExchangeResult:
    """One complete verify reply: rejection needs no ack; all other terminals do."""

    accepted_ack: verify_frames.ReceivedVerifySessionAcceptedAck | None
    terminal: ReceivedVerifySessionTerminal

    def __post_init__(self) -> None:
        if isinstance(self.terminal, verify_frames.ReceivedVerifySessionRejected):
            if self.accepted_ack is not None:
                raise ValueError("verify rejection cannot carry an accepted ack")
            return
        if self.accepted_ack is None:
            raise ValueError("verify terminal requires an accepted ack")


@dataclass(frozen=True, slots=True)
class _AdmittedSourceHistoryState:
    endpoint: weakref.ReferenceType[SourcePortEndpoint]
    session_id: str
    query_message_id: str
    result_message_id: str
    reply_to: str
    correlation_id: str | None
    query: SourceHistoryQueryV1
    payload: _HistoryResult


_ADMITTED_RESULTS: dict[
    int,
    tuple[weakref.ReferenceType["AdmittedSourceHistoryResult"], _AdmittedSourceHistoryState],
] = {}
_ADMITTED_RESULTS_LOCK = threading.Lock()
_ENDPOINTS: dict[int, weakref.ReferenceType["SourcePortEndpoint"]] = {}
_ENDPOINTS_LOCK = threading.Lock()


class AdmittedSourceHistoryResult:
    """Factory-only history result authenticated by one live shared Source Port."""

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
    def payload(self) -> _HistoryResult:
        return _admitted_state(self).payload

    def __copy__(self) -> Never:
        raise TypeError("AdmittedSourceHistoryResult cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("AdmittedSourceHistoryResult cannot be copied")

    def __reduce_ex__(self, _: SupportsIndex) -> Never:
        raise TypeError("AdmittedSourceHistoryResult cannot be serialized")


class SourcePortEndpoint:
    """Private nominal boundary implemented only by factory-created ready endpoints."""

    __slots__ = ()

    def source_port_session(self) -> PostHandshakeSourcePortSession:
        raise TypeError("source port endpoint must be factory-created")

    def _send_source_port_frame(self, frame: bytes, deadline: float) -> None:
        raise TypeError("source port endpoint must be factory-created")

    def _receive_source_port_messages(self, deadline: float) -> tuple[ReceivedSourcePortMessage, ...]:
        raise TypeError("source port endpoint must be factory-created")

    def _begin_source_port_exchange(self) -> Literal["acquired", "in_flight", "unusable"]:
        raise TypeError("source port endpoint must be factory-created")

    def _finish_source_port_exchange(self, *, succeeded: bool) -> None:
        raise TypeError("source port endpoint must be factory-created")


def _register_source_port_endpoint(endpoint: SourcePortEndpoint) -> None:
    """Register one factory-created endpoint without creating any transport state."""
    if not isinstance(endpoint, SourcePortEndpoint):
        raise TypeError("source port endpoint must be factory-created")
    endpoint_id = id(endpoint)

    def finalize(_: weakref.ReferenceType[SourcePortEndpoint]) -> None:
        with _ENDPOINTS_LOCK:
            _ENDPOINTS.pop(endpoint_id, None)

    with _ENDPOINTS_LOCK:
        _ENDPOINTS[endpoint_id] = weakref.ref(endpoint, finalize)


def exchange_source_history(
    endpoint: SourcePortEndpoint,
    query: SourceHistoryQueryV1,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
) -> AdmittedSourceHistoryResult:
    """Run one strict history query over an already-ready shared transport."""
    normalized_timeout = _validated_timeout(timeout)
    source_port = _require_endpoint(endpoint)
    if type(query) is not SourceHistoryQueryV1:
        raise TypeError("query must be a SourceHistoryQueryV1")
    _begin_history_exchange(endpoint)
    succeeded = False
    try:
        deadline = time.monotonic() + normalized_timeout
        message_id = secrets.token_hex(16)
        frame = source_port.encode_query(
            message_id=message_id,
            correlation_id=secrets.token_hex(16),
            payload=query,
        )
        endpoint._send_source_port_frame(frame, deadline)
        received = _receive_one_history_result(endpoint, deadline)
        if received.reply_to != message_id:
            raise history_frames.SourceHistoryAdmissionError(history_frames.SourceHistoryAdmissionReason.UNEXPECTED_MESSAGE)
        succeeded = True
        return _new_admitted_result(endpoint, query=query, query_message_id=message_id, received=received)
    finally:
        endpoint._finish_source_port_exchange(succeeded=succeeded)


def exchange_verify_session(
    endpoint: SourcePortEndpoint,
    request: VerifySessionRequestV1,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
) -> VerifySessionExchangeResult:
    """Submit verify-session: a fresh rejection is terminal; durable terminals require ack."""
    normalized_timeout = _validated_timeout(timeout)
    source_port = _require_endpoint(endpoint)
    if type(request) is not VerifySessionRequestV1:
        raise TypeError("request must be a VerifySessionRequestV1")
    _begin_verify_exchange(endpoint)
    succeeded = False
    try:
        deadline = time.monotonic() + normalized_timeout
        message_id = secrets.token_hex(16)
        frame = source_port.encode_submit(
            message_id=message_id,
            correlation_id=request.identity.correlation_id,
            payload=request,
        )
        endpoint._send_source_port_frame(frame, deadline)
        ack: verify_frames.ReceivedVerifySessionAcceptedAck | None = None
        while True:
            messages = endpoint._receive_source_port_messages(deadline)
            if not messages:
                continue
            for message in messages:
                if getattr(message, "reply_to", None) != message_id:
                    raise SourcePortTransportFrameError("source_port_wrong_reply")
                if isinstance(message, verify_frames.ReceivedVerifySessionAcceptedAck):
                    if ack is not None:
                        raise SourcePortTransportFrameError("source_port_verify_session_response_state_mismatch")
                    ack = message
                    continue
                if isinstance(
                    message,
                    (
                        verify_frames.ReceivedVerifySessionRejected,
                        verify_frames.ReceivedVerifySessionResult,
                        verify_frames.ReceivedVerifySessionFailure,
                        verify_frames.ReceivedVerifySessionReconcileRequired,
                    ),
                ):
                    if isinstance(message, verify_frames.ReceivedVerifySessionRejected):
                        if ack is not None:
                            raise SourcePortTransportFrameError("source_port_verify_session_response_state_mismatch")
                        source_port.require_frame_boundary()
                        succeeded = True
                        return VerifySessionExchangeResult(accepted_ack=None, terminal=message)
                    if ack is None:
                        raise SourcePortTransportFrameError("source_port_verify_session_response_state_mismatch")
                    source_port.require_frame_boundary()
                    succeeded = True
                    return VerifySessionExchangeResult(accepted_ack=ack, terminal=message)
                raise SourcePortTransportFrameError("source_port_unexpected_direction")
    finally:
        endpoint._finish_source_port_exchange(succeeded=succeeded)


def send_source_port_frame(
    endpoint: SourcePortEndpoint,
    frame: bytes,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
) -> None:
    """Write one already-authenticated frame through a live factory endpoint."""
    _require_endpoint(endpoint)
    if type(frame) is not bytes:
        raise TypeError("frame must be bytes")
    endpoint._send_source_port_frame(frame, time.monotonic() + _validated_timeout(timeout))


def receive_source_port_messages(
    endpoint: SourcePortEndpoint,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
) -> tuple[ReceivedSourcePortMessage, ...]:
    """Read and parse one bounded pipe chunk through the shared framing state."""
    _require_endpoint(endpoint)
    return endpoint._receive_source_port_messages(time.monotonic() + _validated_timeout(timeout))


def wait_for_parent_eof(endpoint: SourcePortEndpoint) -> None:
    """Drain the shared parser until the parent closes its end of the pipe."""
    _require_endpoint(endpoint)
    while True:
        try:
            endpoint._receive_source_port_messages(float("inf"))
        except SidecarReadinessError as error:
            if error.reason is not SidecarReadinessReason.EOF:
                raise
            return


def serve_source_history_query(
    endpoint: SourcePortEndpoint,
    reader: object,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
) -> _HistoryResult:
    """Serve one strict history query over the shared post-handshake transport."""
    normalized_timeout = _validated_timeout(timeout)
    _require_endpoint(endpoint)
    _begin_history_exchange(endpoint)
    succeeded = False
    try:
        deadline = time.monotonic() + normalized_timeout
        received = _receive_one_history_query(endpoint, deadline)
        result = _serve_received_history_query(endpoint, reader, received, deadline=deadline)
        succeeded = True
        return result
    finally:
        endpoint._finish_source_port_exchange(succeeded=succeeded)


def _serve_received_history_query(
    endpoint: SourcePortEndpoint,
    reader: object,
    received: history_frames.ReceivedHistoryQuery,
    *,
    deadline: float,
) -> _HistoryResult:
    source_port = _require_endpoint(endpoint)
    query = _reader_query(reader, received.payload, deadline)
    try:
        result = query(received.payload, deadline=deadline)
    except TimeoutError:
        raise SidecarReadinessError(SidecarReadinessReason.READ_TIMEOUT) from None
    except (OSError, RuntimeError, TypeError, ValueError):
        result = SourceHistoryUnavailable.model_validate(
            {
                **received.payload.model_dump(exclude={"contract_version"}),
                "contract_version": "seektalent.source-port.query.result/v1",
                "outcome": "history_unavailable",
                "reason": "unreadable",
            },
            strict=True,
        )
    if not isinstance(
        result,
        (SourceHistoryMatched, SourceHistoryNotFound, SourceHistoryIdentityConflict, SourceHistoryUnavailable),
    ):
        raise history_frames.SourceHistoryAdmissionError(history_frames.SourceHistoryAdmissionReason.READER_RESULT_INVALID)
    frame = source_port.encode_history_result(
        message_id=secrets.token_hex(16),
        reply_to=received.message_id,
        payload=result,
    )
    endpoint._send_source_port_frame(frame, deadline)
    return result


def serve_test_source_port(
    endpoint: SourcePortEndpoint,
    history_reader: object,
    verify_composition: object,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
) -> None:
    """Serve the explicit native-test history and deterministic verify fake over one pipe."""
    normalized_timeout = _validated_timeout(timeout)
    _require_endpoint(endpoint)
    from seektalent.source_port.verify_session_journal_effect import VerifySessionJournalEffectComposition

    if type(verify_composition) is not VerifySessionJournalEffectComposition:
        raise TypeError("verify composition must be factory-created")
    while True:
        try:
            deadline = time.monotonic() + normalized_timeout
            messages = endpoint._receive_source_port_messages(deadline)
        except SidecarReadinessError as error:
            if error.reason is SidecarReadinessReason.EOF:
                return
            raise
        for message in messages:
            if isinstance(message, history_frames.ReceivedHistoryQuery):
                _begin_history_exchange(endpoint)
                succeeded = False
                try:
                    _serve_received_history_query(endpoint, history_reader, message, deadline=deadline)
                    succeeded = True
                finally:
                    endpoint._finish_source_port_exchange(succeeded=succeeded)
                continue
            if not isinstance(message, verify_frames.ReceivedVerifySessionSubmit):
                raise SourcePortTransportFrameError("source_port_unexpected_direction")
            _begin_verify_exchange(endpoint)
            succeeded = False
            try:
                exchange = verify_composition.handle_submit(message)
                _send_verify_exchange(endpoint, exchange, deadline=deadline)
                succeeded = True
            finally:
                endpoint._finish_source_port_exchange(succeeded=succeeded)


def serve_test_source_history_database(
    endpoint: SourcePortEndpoint,
    path: object,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
) -> None:
    """Serve the existing explicit native-test history database over the shared transport."""
    from pathlib import Path

    from seektalent.source_port.history_sqlite_reader import SourceHistorySQLiteReader

    if not isinstance(path, Path):
        raise TypeError("path must be a Path")
    reader = SourceHistorySQLiteReader(path)
    while True:
        try:
            serve_source_history_query(endpoint, reader, timeout=timeout)
        except SidecarReadinessError as error:
            if error.reason is SidecarReadinessReason.EOF:
                return
            raise


def _send_verify_exchange(endpoint: SourcePortEndpoint, exchange: object, *, deadline: float) -> None:
    frames = getattr(exchange, "outbound_frames", None)
    if type(frames) is not tuple or not all(type(frame) is bytes for frame in frames):
        raise TypeError("verify composition exchange must be factory-created")
    deadline_at = getattr(exchange, "arrival_deadline_at", None)
    if deadline_at is not None and (isinstance(deadline_at, bool) or not isinstance(deadline_at, (int, float))):
        raise TypeError("verify composition exchange deadline is invalid")
    write_deadline = deadline if deadline_at is None else float(deadline_at)
    for frame in frames:
        endpoint._send_source_port_frame(frame, write_deadline)
    pending_effect = getattr(exchange, "pending_effect", None)
    if pending_effect is None:
        return
    consume = getattr(pending_effect, "consume", None)
    if not callable(consume):
        raise TypeError("verify pending effect must be factory-created")
    terminal = consume()
    terminal_frames = getattr(terminal, "outbound_frames", None)
    if type(terminal_frames) is not tuple or not all(type(frame) is bytes for frame in terminal_frames):
        raise TypeError("verify terminal exchange must be factory-created")
    for frame in terminal_frames:
        endpoint._send_source_port_frame(frame, write_deadline)


def _require_endpoint(endpoint: SourcePortEndpoint) -> PostHandshakeSourcePortSession:
    if not isinstance(endpoint, SourcePortEndpoint):
        raise TypeError("source port endpoint must be factory-created")
    with _ENDPOINTS_LOCK:
        reference = _ENDPOINTS.get(id(endpoint))
    if reference is None or reference() is not endpoint:
        raise TypeError("source port endpoint must be factory-created")
    session = endpoint.source_port_session()
    if type(session) is not PostHandshakeSourcePortSession:
        raise TypeError("source port endpoint must be factory-created")
    return session


def require_live_admitted_source_history_result(result: object) -> AdmittedSourceHistoryResult:
    """Require exact factory provenance while the admitting transport remains live."""
    if type(result) is not AdmittedSourceHistoryResult:
        raise TypeError("AdmittedSourceHistoryResult must be a live factory result")
    state = _admitted_state(result)
    endpoint = state.endpoint()
    if endpoint is None:
        raise TypeError("AdmittedSourceHistoryResult must be a live factory result")
    if _require_endpoint(endpoint)._session_id != state.session_id:
        raise TypeError("AdmittedSourceHistoryResult must be a live factory result")
    return result


def _new_admitted_result(
    endpoint: SourcePortEndpoint,
    *,
    query: SourceHistoryQueryV1,
    query_message_id: str,
    received: history_frames.ReceivedHistoryResult,
) -> AdmittedSourceHistoryResult:
    admitted = object.__new__(AdmittedSourceHistoryResult)
    admitted_id = id(admitted)
    state = _AdmittedSourceHistoryState(
        endpoint=weakref.ref(endpoint),
        session_id=_require_endpoint(endpoint)._session_id,
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


def _begin_history_exchange(endpoint: SourcePortEndpoint) -> None:
    outcome = endpoint._begin_source_port_exchange()
    if outcome == "acquired":
        return
    reason = (
        history_frames.SourceHistoryAdmissionReason.QUERY_IN_FLIGHT
        if outcome == "in_flight"
        else history_frames.SourceHistoryAdmissionReason.SESSION_UNUSABLE
    )
    raise history_frames.SourceHistoryAdmissionError(reason)


def _begin_verify_exchange(endpoint: SourcePortEndpoint) -> None:
    outcome = endpoint._begin_source_port_exchange()
    if outcome != "acquired":
        raise SourcePortTransportFrameError("source_port_session_closed")


def _receive_one_history_result(
    endpoint: SourcePortEndpoint,
    deadline: float,
) -> history_frames.ReceivedHistoryResult:
    source_port = endpoint.source_port_session()
    while True:
        messages = endpoint._receive_source_port_messages(deadline)
        if not messages:
            continue
        if len(messages) != 1:
            raise history_frames.SourceHistoryAdmissionError(history_frames.SourceHistoryAdmissionReason.MULTIPLE_MESSAGES)
        source_port.require_frame_boundary()
        message = messages[0]
        if not isinstance(message, history_frames.ReceivedHistoryResult):
            raise history_frames.SourceHistoryAdmissionError(history_frames.SourceHistoryAdmissionReason.UNEXPECTED_MESSAGE)
        return message


def _receive_one_history_query(
    endpoint: SourcePortEndpoint,
    deadline: float,
) -> history_frames.ReceivedHistoryQuery:
    source_port = _require_endpoint(endpoint)
    while True:
        messages = endpoint._receive_source_port_messages(deadline)
        if not messages:
            continue
        if len(messages) != 1:
            raise history_frames.SourceHistoryAdmissionError(history_frames.SourceHistoryAdmissionReason.MULTIPLE_MESSAGES)
        source_port.require_frame_boundary()
        message = messages[0]
        if not isinstance(message, history_frames.ReceivedHistoryQuery):
            raise history_frames.SourceHistoryAdmissionError(history_frames.SourceHistoryAdmissionReason.UNEXPECTED_MESSAGE)
        return message


def _reader_query(reader: object, request: SourceHistoryQueryV1, deadline: float):
    query = getattr(reader, "query", None)
    if not callable(query):
        raise history_frames.SourceHistoryAdmissionError(history_frames.SourceHistoryAdmissionReason.READER_RESULT_INVALID)
    try:
        inspect.signature(query).bind(request, deadline=deadline)
    except (TypeError, ValueError):
        raise history_frames.SourceHistoryAdmissionError(
            history_frames.SourceHistoryAdmissionReason.READER_RESULT_INVALID
        ) from None
    return query


__all__ = [
    "AdmittedSourceHistoryResult",
    "PostHandshakeSourcePortSession",
    "ReceivedSourcePortMessage",
    "SourcePortEndpoint",
    "SourcePortTransportFrameError",
    "VerifySessionExchangeResult",
    "exchange_source_history",
    "exchange_verify_session",
    "receive_source_port_messages",
    "require_live_admitted_source_history_result",
    "send_source_port_frame",
    "serve_source_history_query",
    "serve_test_source_history_database",
    "serve_test_source_port",
    "wait_for_parent_eof",
]
