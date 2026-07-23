"""Factory-only, exactly-once authority for one pending verify-session effect."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import threading
from typing import TYPE_CHECKING, Never
import weakref

if TYPE_CHECKING:
    from seektalent.source_port.verify_session_journal_effect import VerifySessionJournalEffectExchange


@dataclass(slots=True)
class _PendingEffectState:
    consume_effect: Callable[[], VerifySessionJournalEffectExchange]
    lock: threading.Lock = field(default_factory=threading.Lock)
    consumed: bool = False


_AUTHORITIES: dict[
    int,
    tuple[weakref.ReferenceType["VerifySessionPendingEffectAuthority"], _PendingEffectState],
] = {}
_AUTHORITY_LOCK = threading.Lock()


class VerifySessionPendingEffectAuthority:
    """One live authority that may advance a durable dispatch intent exactly once."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("VerifySessionPendingEffectAuthority is factory-only")

    def consume(self) -> VerifySessionJournalEffectExchange:
        """Consume this authority once and synchronously advance its pending effect."""
        state = _consume_state(self)
        return state.consume_effect()

    def __copy__(self) -> Never:
        raise TypeError("VerifySessionPendingEffectAuthority cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("VerifySessionPendingEffectAuthority cannot be copied")

    def __reduce_ex__(self, _: object) -> Never:
        raise TypeError("VerifySessionPendingEffectAuthority cannot be serialized")

    def __repr__(self) -> str:
        return "VerifySessionPendingEffectAuthority()"


def _create_pending_effect_authority(
    *,
    consume_effect: Callable[[], VerifySessionJournalEffectExchange],
) -> VerifySessionPendingEffectAuthority:
    if not callable(consume_effect):
        raise TypeError("pending effect consumer must be callable")
    authority = object.__new__(VerifySessionPendingEffectAuthority)
    authority_id = id(authority)
    state = _PendingEffectState(consume_effect=consume_effect)

    def finalize(_: weakref.ReferenceType[VerifySessionPendingEffectAuthority]) -> None:
        with _AUTHORITY_LOCK:
            _AUTHORITIES.pop(authority_id, None)

    with _AUTHORITY_LOCK:
        _AUTHORITIES[authority_id] = (weakref.ref(authority, finalize), state)
    return authority


def _consume_state(authority: VerifySessionPendingEffectAuthority) -> _PendingEffectState:
    if type(authority) is not VerifySessionPendingEffectAuthority:
        raise TypeError("VerifySessionPendingEffectAuthority must be a live factory authority")
    with _AUTHORITY_LOCK:
        entry = _AUTHORITIES.get(id(authority))
        if entry is None or entry[0]() is not authority:
            raise TypeError("VerifySessionPendingEffectAuthority must be a live factory authority")
        state = entry[1]
        with state.lock:
            if state.consumed:
                raise TypeError("VerifySessionPendingEffectAuthority has already been consumed")
            state.consumed = True
    return state


__all__ = ["VerifySessionPendingEffectAuthority"]
