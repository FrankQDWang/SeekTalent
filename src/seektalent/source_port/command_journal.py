"""Public factory capabilities for the production-unreachable command journal."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Never
import weakref

from seektalent.source_port import _command_journal_engine as _engine
from seektalent.source_port._command_journal_types import (
    AcceptedCommand,
    CommandJournalConflict,
    CommandJournalConflictReason,
    CommandJournalError,
    CommandJournalErrorReason,
    CommandJournalTransitionDisposition,
    CommandJournalTransitionResult,
)


__all__ = [
    "AcceptedCommand",
    "CommandJournal",
    "CommandJournalConflict",
    "CommandJournalConflictReason",
    "CommandJournalError",
    "CommandJournalErrorReason",
    "CommandJournalSession",
    "CommandJournalTransitionDisposition",
    "CommandJournalTransitionReceipt",
    "create_command_journal",
    "open_command_journal",
]


@dataclass(slots=True)
class _JournalState:
    path: Path


@dataclass(slots=True)
class _SessionState:
    path: Path
    generation: int
    instance_id: str


_JOURNALS: dict[int, tuple[weakref.ReferenceType["CommandJournal"], _JournalState]] = {}
_SESSIONS: dict[int, tuple[weakref.ReferenceType["CommandJournalSession"], _SessionState]] = {}
_FACTORY_LOCK = threading.Lock()
_RECEIPT_TOKEN = object()


class CommandJournalTransitionReceipt(int):
    """A sealed transition result that remains numerically compatible with revisions."""

    def __new__(
        cls,
        _: int,
        *,
        result: CommandJournalTransitionResult,
        token: object,
    ) -> CommandJournalTransitionReceipt:
        if token is not _RECEIPT_TOKEN or type(result) is not CommandJournalTransitionResult:
            raise TypeError("CommandJournalTransitionReceipt is factory-only")
        receipt = int.__new__(cls, result.revision)
        object.__setattr__(receipt, "_result", result)
        object.__setattr__(receipt, "_token", token)
        return receipt

    def __init__(
        self,
        _: int,
        *,
        result: CommandJournalTransitionResult,
        token: object,
    ) -> None:
        return None

    @property
    def disposition(self) -> CommandJournalTransitionDisposition:
        return _receipt_result(self).disposition

    @property
    def startup_generation(self) -> int:
        return _receipt_result(self).startup_generation

    @property
    def revision(self) -> int:
        return int(self)

    @property
    def head_phase(self) -> str:
        return _receipt_result(self).head_phase

    @property
    def accepted_ack_bytes(self) -> bytes | None:
        return _receipt_result(self).accepted_ack_bytes

    @property
    def terminal_reply_bytes(self) -> bytes | None:
        return _receipt_result(self).terminal_reply_bytes

    def __setattr__(self, _: str, __: object) -> Never:
        raise AttributeError("CommandJournalTransitionReceipt is immutable")

    def __copy__(self) -> Never:
        raise TypeError("CommandJournalTransitionReceipt cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("CommandJournalTransitionReceipt cannot be copied")

    def __reduce_ex__(self, _: object) -> Never:
        raise TypeError("CommandJournalTransitionReceipt cannot be serialized")

    def __repr__(self) -> str:
        result = _receipt_result(self)
        return (
            "CommandJournalTransitionReceipt("
            f"revision={int(self)}, disposition={result.disposition.value!r}, "
            f"startup_generation={result.startup_generation}, head_phase={result.head_phase!r})"
        )


class CommandJournal:
    """Factory-only lifecycle for one explicit SQLite command journal."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("CommandJournal is factory-only")

    @property
    def path(self) -> Path:
        return _journal_state(self).path

    def start(self) -> CommandJournalSession:
        """Allocate and persist one fresh sidecar generation capability."""
        state = _journal_state(self)
        generation, instance_id = _engine._start_generation(state.path)
        return _new_session(state.path, generation=generation, instance_id=instance_id)

    def close(self) -> None:
        with _FACTORY_LOCK:
            entry = _JOURNALS.get(id(self))
            if entry is None or entry[0]() is not self:
                raise TypeError("CommandJournal must be a live factory journal")
            _JOURNALS.pop(id(self), None)

    def __copy__(self) -> Never:
        raise TypeError("CommandJournal cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("CommandJournal cannot be copied")

    def __reduce_ex__(self, _: object) -> Never:
        raise TypeError("CommandJournal cannot be serialized")


class CommandJournalSession:
    """Factory-only write authority for one durable sidecar generation."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("CommandJournalSession is factory-only")

    @property
    def generation(self) -> int:
        return _session_state(self).generation

    @property
    def instance_id(self) -> str:
        return _session_state(self).instance_id

    def close(self) -> None:
        with _FACTORY_LOCK:
            entry = _SESSIONS.get(id(self))
            if entry is None or entry[0]() is not self:
                raise TypeError("CommandJournalSession must be a live factory session")
            _SESSIONS.pop(id(self), None)

    def record_accepted(
        self,
        accepted: AcceptedCommand,
        *,
        accepted_ack_bytes: bytes | None = None,
        allow_existing_phase_replay: bool = False,
        allow_transport_replay: bool = False,
        require_existing_replay: bool = False,
    ) -> CommandJournalTransitionReceipt:
        """Atomically persist the accepted phase for one command."""
        state = _session_state(self)
        return _new_transition_receipt(
            _engine._record_accepted(
                path=state.path,
                generation=state.generation,
                instance_id=state.instance_id,
                accepted=accepted,
                accepted_ack_bytes=accepted_ack_bytes,
                allow_existing_phase_replay=allow_existing_phase_replay,
                allow_transport_replay=allow_transport_replay,
                require_existing_replay=require_existing_replay,
            )
        )

    def record_dispatch_intent(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_head_journal_revision: int,
        durable_dispatch_intent_ref: str,
    ) -> CommandJournalTransitionReceipt:
        """Atomically persist a dispatch intent before any external effect."""
        state = _session_state(self)
        return _new_transition_receipt(
            _engine._record_dispatch_intent(
                path=state.path,
                generation=state.generation,
                instance_id=state.instance_id,
                run_id=run_id,
                operation_id=operation_id,
                expected_head_journal_revision=expected_head_journal_revision,
                durable_dispatch_intent_ref=durable_dispatch_intent_ref,
            )
        )

    def record_observed_result(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_head_journal_revision: int,
        result_ref: str,
        result_hash: str,
        terminal_reply_bytes: bytes | None = None,
    ) -> CommandJournalTransitionReceipt:
        """Atomically persist one observed result."""
        state = _session_state(self)
        return _new_transition_receipt(
            _engine._record_observation(
                path=state.path,
                generation=state.generation,
                instance_id=state.instance_id,
                run_id=run_id,
                operation_id=operation_id,
                expected_head_journal_revision=expected_head_journal_revision,
                observation_kind="observed_result",
                observation_ref=result_ref,
                observation_hash=result_hash,
                terminal_reply_bytes=terminal_reply_bytes,
            )
        )

    def record_observed_failure(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_head_journal_revision: int,
        failure_ref: str,
        failure_hash: str,
        terminal_reply_bytes: bytes | None = None,
    ) -> CommandJournalTransitionReceipt:
        """Atomically persist one observed failure."""
        state = _session_state(self)
        return _new_transition_receipt(
            _engine._record_observation(
                path=state.path,
                generation=state.generation,
                instance_id=state.instance_id,
                run_id=run_id,
                operation_id=operation_id,
                expected_head_journal_revision=expected_head_journal_revision,
                observation_kind="observed_failure",
                observation_ref=failure_ref,
                observation_hash=failure_hash,
                terminal_reply_bytes=terminal_reply_bytes,
            )
        )

    def __copy__(self) -> Never:
        raise TypeError("CommandJournalSession cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("CommandJournalSession cannot be copied")

    def __reduce_ex__(self, _: object) -> Never:
        raise TypeError("CommandJournalSession cannot be serialized")


def create_command_journal(path: Path) -> CommandJournal:
    """Create and return one journal capability at an explicit path."""
    _engine._create_database(path)
    return _new_journal(path)


def open_command_journal(path: Path) -> CommandJournal:
    """Open and validate one existing journal without repairing it."""
    _engine._validate_existing_database(path)
    return _new_journal(path)


def _new_journal(path: Path) -> CommandJournal:
    journal = object.__new__(CommandJournal)
    journal_id = id(journal)

    def finalize(_: weakref.ReferenceType[CommandJournal]) -> None:
        with _FACTORY_LOCK:
            _JOURNALS.pop(journal_id, None)

    with _FACTORY_LOCK:
        _JOURNALS[journal_id] = (weakref.ref(journal, finalize), _JournalState(path=path))
    return journal


def _new_session(path: Path, *, generation: int, instance_id: str) -> CommandJournalSession:
    session = object.__new__(CommandJournalSession)
    session_id = id(session)

    def finalize(_: weakref.ReferenceType[CommandJournalSession]) -> None:
        with _FACTORY_LOCK:
            _SESSIONS.pop(session_id, None)

    state = _SessionState(path=path, generation=generation, instance_id=instance_id)
    with _FACTORY_LOCK:
        _SESSIONS[session_id] = (weakref.ref(session, finalize), state)
    return session


def _new_transition_receipt(result: CommandJournalTransitionResult) -> CommandJournalTransitionReceipt:
    return CommandJournalTransitionReceipt(result.revision, result=result, token=_RECEIPT_TOKEN)


def _receipt_result(receipt: CommandJournalTransitionReceipt) -> CommandJournalTransitionResult:
    if type(receipt) is not CommandJournalTransitionReceipt:
        raise TypeError("CommandJournalTransitionReceipt must be a live factory receipt")
    try:
        result = object.__getattribute__(receipt, "_result")
        token = object.__getattribute__(receipt, "_token")
    except AttributeError:
        raise TypeError("CommandJournalTransitionReceipt must be a live factory receipt") from None
    if token is not _RECEIPT_TOKEN or type(result) is not CommandJournalTransitionResult:
        raise TypeError("CommandJournalTransitionReceipt must be a live factory receipt")
    return result


def _journal_state(journal: CommandJournal) -> _JournalState:
    if type(journal) is not CommandJournal:
        raise TypeError("CommandJournal must be a live factory journal")
    with _FACTORY_LOCK:
        entry = _JOURNALS.get(id(journal))
    if entry is None or entry[0]() is not journal:
        raise TypeError("CommandJournal must be a live factory journal")
    return entry[1]


def _session_state(session: CommandJournalSession) -> _SessionState:
    if type(session) is not CommandJournalSession:
        raise TypeError("CommandJournalSession must be a live factory session")
    with _FACTORY_LOCK:
        entry = _SESSIONS.get(id(session))
    if entry is None or entry[0]() is not session:
        raise TypeError("CommandJournalSession must be a live factory session")
    return entry[1]
