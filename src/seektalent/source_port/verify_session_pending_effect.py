"""Factory-only, exactly-once authority for one pending verify-session effect."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import threading
from typing import Generic, Never, TypeVar
import weakref


ResultT = TypeVar("ResultT")


@dataclass(slots=True)
class _PendingEffectState(Generic[ResultT]):
    consume_effect: Callable[[], ResultT]
    lock: threading.Lock = field(default_factory=threading.Lock)
    consumed: bool = False


_AUTHORITIES: dict[int, weakref.ReferenceType[object]] = {}
_AUTHORITY_LOCK = threading.Lock()
_FACTORY_TOKEN = object()


class VerifySessionPendingEffectAuthority(Generic[ResultT]):
    """One live authority that may advance a durable dispatch intent exactly once."""

    __slots__ = ("__state", "__weakref__")
    __state: _PendingEffectState[ResultT]

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("VerifySessionPendingEffectAuthority is factory-only")

    def consume(self) -> ResultT:
        """Consume this authority once and synchronously advance its pending effect."""
        state = _authority_state(self)
        with state.lock:
            if state.consumed:
                raise TypeError("VerifySessionPendingEffectAuthority has already been consumed")
            state.consumed = True
        return state.consume_effect()

    def _install_state(self, factory_token: object, state: _PendingEffectState[ResultT]) -> None:
        if factory_token is not _FACTORY_TOKEN:
            raise TypeError("VerifySessionPendingEffectAuthority is factory-only")
        self.__state = state

    def _state(self) -> _PendingEffectState[ResultT]:
        return self.__state

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
    consume_effect: Callable[[], ResultT],
) -> VerifySessionPendingEffectAuthority[ResultT]:
    if not callable(consume_effect):
        raise TypeError("pending effect consumer must be callable")
    authority = object.__new__(VerifySessionPendingEffectAuthority)
    authority_id = id(authority)
    state = _PendingEffectState(consume_effect=consume_effect)
    authority._install_state(_FACTORY_TOKEN, state)

    def finalize(_: weakref.ReferenceType[object]) -> None:
        with _AUTHORITY_LOCK:
            _AUTHORITIES.pop(authority_id, None)

    with _AUTHORITY_LOCK:
        _AUTHORITIES[authority_id] = weakref.ref(authority, finalize)
    return authority


def _authority_state(authority: VerifySessionPendingEffectAuthority[ResultT]) -> _PendingEffectState[ResultT]:
    if type(authority) is not VerifySessionPendingEffectAuthority:
        raise TypeError("VerifySessionPendingEffectAuthority must be a live factory authority")
    with _AUTHORITY_LOCK:
        reference = _AUTHORITIES.get(id(authority))
        if reference is None or reference() is not authority:
            raise TypeError("VerifySessionPendingEffectAuthority must be a live factory authority")
    return authority._state()


__all__ = ["VerifySessionPendingEffectAuthority"]
