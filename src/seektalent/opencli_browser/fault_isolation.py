from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import TypeVar


T = TypeVar("T")


class _FailureIsolation:
    def __init__(self, report_failure: Callable[[Exception], None] | None) -> None:
        self._report_failure = report_failure

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        if not isinstance(exc, Exception):
            return False
        if self._report_failure is not None:
            with _FailureIsolation(None):
                self._report_failure(exc)
        return True


def isolated_call(action: Callable[[], T], report_failure: Callable[[Exception], None]) -> T | None:
    """Run an optional browser safeguard without letting it alter the primary operation."""
    result: T | None = None
    with _FailureIsolation(report_failure):
        result = action()
    return result
